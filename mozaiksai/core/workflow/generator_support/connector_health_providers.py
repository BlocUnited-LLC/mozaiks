"""Built-in ConnectorHealthProvider implementations for all catalog integration services.

Call ``register_builtin_providers()`` once at process startup (e.g. in the host
lifespan) to enable server-side health checks for every service declared in
``factory_app/build_context/integrations/catalog.yaml``.

Design principles
-----------------
* All checks are asynchronous and respect a hard 5-second timeout.
* Secrets are accessed exclusively through ``ConnectorSecretReader``;
  they are never logged, stored, or propagated to response bodies.
* Multi-credential services (Twilio, S3, OAuth) store their credentials as a
  JSON object in the vault secret. Helpers try JSON first, fall back gracefully.
* Optional Python dependencies (asyncpg) are guarded with try/except import.
  ``motor`` and ``redis[asyncio]`` are declared core deps and imported directly.
* Connection errors return ``"unknown"`` (the service may be temporarily
  unreachable). Authentication failures return ``"unhealthy"``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .connector_health import (
    ConnectorHealthContext,
    ConnectorHealthProvider,
    ConnectorHealthResult,
    ConnectorSecretReader,
    register_connector_health_provider,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0  # seconds — applied to every outbound health-check request


# --------------------------------------------------------------------------- #
# Internal helpers                                                              #
# --------------------------------------------------------------------------- #


def _parse_secret_json(value: str | None) -> dict[str, str]:
    """Try to parse a vault secret value as JSON; return empty dict on failure."""
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


async def _http_check(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    basic_auth: tuple[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute an HTTP request and return (status_code, response_body_dict)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.request(
            method,
            url,
            headers=headers or {},
            auth=basic_auth,
            params=params,
        )
        try:
            body: dict[str, Any] = resp.json()
        except Exception:
            body = {}
    return resp.status_code, body


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _auth_result(status: int, *, service: str) -> ConnectorHealthResult:
    """Map a common HTTP status code to a health result for credential checks."""
    if status == 200:
        return ConnectorHealthResult(status="healthy", message=f"{service} credential is valid.")
    if status in (401, 403):
        return ConnectorHealthResult(
            status="unhealthy",
            message=f"{service} credential is invalid or has insufficient permissions.",
            error_code="auth_failed",
        )
    if status == 429:
        return ConnectorHealthResult(
            status="healthy",
            message=f"{service} credential is valid (rate limited).",
            safe_details={"rate_limited": True},
        )
    return ConnectorHealthResult(
        status="unknown",
        message=f"{service} returned unexpected HTTP {status}.",
        error_code="unexpected_status",
    )


def _connection_error(service: str) -> ConnectorHealthResult:
    return ConnectorHealthResult(
        status="unknown",
        message=f"Cannot reach {service}. The service may be temporarily unavailable.",
        error_code="connection_error",
    )


def _timeout_error(service: str) -> ConnectorHealthResult:
    return ConnectorHealthResult(
        status="unknown",
        message=f"{service} health check timed out.",
        error_code="timeout",
    )


def _no_secret(service: str) -> ConnectorHealthResult:
    return ConnectorHealthResult(
        status="unknown",
        message=f"{service} secret is not yet configured.",
        error_code="secret_unavailable",
    )


# --------------------------------------------------------------------------- #
# LLM providers                                                                #
# --------------------------------------------------------------------------- #


class _OpenAIProvider:
    provider_id = "openai"
    supported_integration_ids = ["openai"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("OpenAI")
        try:
            code, _ = await _http_check(
                "GET",
                "https://api.openai.com/v1/models",
                headers=_bearer(handle.value),
            )
        except httpx.ConnectError:
            return _connection_error("OpenAI")
        except httpx.TimeoutException:
            return _timeout_error("OpenAI")
        return _auth_result(code, service="OpenAI")


class _AnthropicProvider:
    provider_id = "anthropic"
    supported_integration_ids = ["anthropic"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Anthropic")
        try:
            code, _ = await _http_check(
                "GET",
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": handle.value,
                    "anthropic-version": "2023-06-01",
                },
            )
        except httpx.ConnectError:
            return _connection_error("Anthropic")
        except httpx.TimeoutException:
            return _timeout_error("Anthropic")
        return _auth_result(code, service="Anthropic")


class _GeminiProvider:
    provider_id = "gemini"
    supported_integration_ids = ["gemini"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Gemini")
        try:
            code, _ = await _http_check(
                "GET",
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": handle.value},
            )
        except httpx.ConnectError:
            return _connection_error("Gemini")
        except httpx.TimeoutException:
            return _timeout_error("Gemini")
        if code == 200:
            return ConnectorHealthResult(status="healthy", message="Gemini API key is valid.")
        if code in (400, 401, 403):
            return ConnectorHealthResult(
                status="unhealthy",
                message="Gemini API key is invalid or has insufficient permissions.",
                error_code="auth_failed",
            )
        return ConnectorHealthResult(
            status="unknown",
            message=f"Gemini API returned unexpected HTTP {code}.",
            error_code="unexpected_status",
        )


# --------------------------------------------------------------------------- #
# Payment providers                                                            #
# --------------------------------------------------------------------------- #


class _MozaiksPayProvider:
    """Health check for the MozaiksPay hosted billing integration.

    Expects the secret to be a JSON object with ``api_key`` (required) and
    optionally ``api_base``. ``api_base`` may also be provided via
    ``public_config``.
    """

    provider_id = "mozaikspay"
    supported_integration_ids = ["mozaikspay"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Mozaiks Pay")

        creds = _parse_secret_json(handle.value)
        api_key = creds.get("api_key") or handle.value  # bare string fallback
        api_base = (
            creds.get("api_base")
            or public_config.get("api_base")
            or public_config.get("MOZAIKSPAY_API_BASE")
            or ""
        ).rstrip("/")

        if not api_base:
            return ConnectorHealthResult(
                status="not_configured",
                message="Mozaiks Pay API base URL is not configured.",
                error_code="missing_api_base",
            )

        try:
            code, _ = await _http_check(
                "GET",
                f"{api_base}/api/mozaikspay/v1/health",
                headers=_bearer(api_key),
            )
        except httpx.ConnectError:
            return _connection_error("Mozaiks Pay")
        except httpx.TimeoutException:
            return _timeout_error("Mozaiks Pay")
        return _auth_result(code, service="Mozaiks Pay")


# --------------------------------------------------------------------------- #
# Email providers                                                              #
# --------------------------------------------------------------------------- #


class _ResendProvider:
    provider_id = "resend"
    supported_integration_ids = ["resend"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Resend")
        try:
            code, _ = await _http_check(
                "GET",
                "https://api.resend.com/domains",
                headers=_bearer(handle.value),
            )
        except httpx.ConnectError:
            return _connection_error("Resend")
        except httpx.TimeoutException:
            return _timeout_error("Resend")
        return _auth_result(code, service="Resend")


class _SendGridProvider:
    provider_id = "sendgrid"
    supported_integration_ids = ["sendgrid"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("SendGrid")
        try:
            # /v3/scopes lists the API key's permissions — lightweight validation.
            code, _ = await _http_check(
                "GET",
                "https://api.sendgrid.com/v3/scopes",
                headers=_bearer(handle.value),
            )
        except httpx.ConnectError:
            return _connection_error("SendGrid")
        except httpx.TimeoutException:
            return _timeout_error("SendGrid")
        return _auth_result(code, service="SendGrid")


# --------------------------------------------------------------------------- #
# SMS providers                                                                #
# --------------------------------------------------------------------------- #


class _TwilioProvider:
    """Health check for the Twilio integration.

    Twilio requires ``account_sid`` + ``auth_token``. These should be stored as
    a JSON object in the vault secret:
        ``{"account_sid": "ACxxx", "auth_token": "xxx"}``

    ``account_sid`` may also be provided via ``public_config`` when the
    operator prefers to keep it non-secret. ``auth_token`` is always treated as
    a secret and must come from the vault.
    """

    provider_id = "twilio"
    supported_integration_ids = ["twilio"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Twilio")

        creds = _parse_secret_json(handle.value)
        account_sid = (
            creds.get("account_sid")
            or public_config.get("account_sid")
            or public_config.get("TWILIO_ACCOUNT_SID")
            or ""
        ).strip()
        auth_token = (creds.get("auth_token") or "").strip()

        # Bare-string secret: treat the whole value as the auth_token and
        # look for account_sid exclusively in public_config.
        if not auth_token and not creds:
            auth_token = handle.value.strip()

        if not account_sid or not auth_token:
            return ConnectorHealthResult(
                status="not_configured",
                message=(
                    "Twilio requires both TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN. "
                    "Store them as a JSON object in the connector secret."
                ),
                error_code="missing_credentials",
            )

        try:
            code, body = await _http_check(
                "GET",
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
                basic_auth=(account_sid, auth_token),
            )
        except httpx.ConnectError:
            return _connection_error("Twilio")
        except httpx.TimeoutException:
            return _timeout_error("Twilio")

        if code == 200:
            return ConnectorHealthResult(
                status="healthy",
                message="Twilio credentials are valid.",
                safe_details={"account_status": body.get("status")},
            )
        if code in (401, 403):
            return ConnectorHealthResult(
                status="unhealthy",
                message="Twilio credentials are invalid.",
                error_code="auth_failed",
            )
        return ConnectorHealthResult(
            status="unknown",
            message=f"Twilio API returned unexpected HTTP {code}.",
            error_code="unexpected_status",
        )


# --------------------------------------------------------------------------- #
# Notification providers                                                       #
# --------------------------------------------------------------------------- #


class _SlackProvider:
    provider_id = "slack"
    supported_integration_ids = ["slack"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Slack")
        try:
            code, body = await _http_check(
                "POST",
                "https://slack.com/api/auth.test",
                headers=_bearer(handle.value),
            )
        except httpx.ConnectError:
            return _connection_error("Slack")
        except httpx.TimeoutException:
            return _timeout_error("Slack")

        if code != 200:
            return ConnectorHealthResult(
                status="unknown",
                message=f"Slack API returned unexpected HTTP {code}.",
                error_code="unexpected_status",
            )

        if body.get("ok"):
            return ConnectorHealthResult(
                status="healthy",
                message="Slack bot token is valid.",
                safe_details={"team": body.get("team")},
            )
        slack_error = body.get("error", "unknown_error")
        if slack_error in ("invalid_auth", "not_authed", "token_revoked"):
            return ConnectorHealthResult(
                status="unhealthy",
                message="Slack bot token is invalid or revoked.",
                error_code="auth_failed",
            )
        return ConnectorHealthResult(
            status="unknown",
            message=f"Slack auth.test returned error: {slack_error}.",
            error_code=slack_error,
        )


# --------------------------------------------------------------------------- #
# Source control providers                                                     #
# --------------------------------------------------------------------------- #


class _GitHubProvider:
    provider_id = "github"
    supported_integration_ids = ["github"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("GitHub")
        try:
            code, body = await _http_check(
                "GET",
                "https://api.github.com/user",
                headers={
                    **_bearer(handle.value),
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        except httpx.ConnectError:
            return _connection_error("GitHub")
        except httpx.TimeoutException:
            return _timeout_error("GitHub")

        if code == 200:
            return ConnectorHealthResult(
                status="healthy",
                message="GitHub token is valid.",
                safe_details={"login": body.get("login")},
            )
        if code in (401, 403):
            return ConnectorHealthResult(
                status="unhealthy",
                message="GitHub token is invalid or has insufficient scopes.",
                error_code="auth_failed",
            )
        return ConnectorHealthResult(
            status="unknown",
            message=f"GitHub API returned unexpected HTTP {code}.",
            error_code="unexpected_status",
        )


# --------------------------------------------------------------------------- #
# Storage providers                                                            #
# --------------------------------------------------------------------------- #


class _S3Provider:
    """Health check for AWS S3 credentials.

    Expects the vault secret to be a JSON object:
        ``{"access_key_id": "AKIA...", "secret_access_key": "xxx", "bucket": "my-bucket"}``

    When ``aioboto3`` or ``boto3`` is unavailable the check falls back to a
    lightweight format validation of the access key ID and returns
    ``"configured"`` rather than ``"healthy"`` so operators know the live
    reachability was not verified.
    """

    provider_id = "s3"
    supported_integration_ids = ["s3"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("AWS S3")

        creds = _parse_secret_json(handle.value)
        access_key_id = (
            creds.get("access_key_id")
            or creds.get("AWS_ACCESS_KEY_ID")
            or public_config.get("access_key_id")
            or ""
        ).strip()
        secret_access_key = (
            creds.get("secret_access_key")
            or creds.get("AWS_SECRET_ACCESS_KEY")
            or ""
        ).strip()

        if not access_key_id or not secret_access_key:
            return ConnectorHealthResult(
                status="not_configured",
                message=(
                    "S3 requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY. "
                    "Store them as a JSON object in the connector secret."
                ),
                error_code="missing_credentials",
            )

        # Try boto3 for a live STS identity check.
        try:
            import boto3  # type: ignore[import-untyped]

            sts = boto3.client(
                "sts",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
            )
            # Run the blocking STS call in a thread to avoid blocking the event loop.
            import asyncio

            caller = await asyncio.get_event_loop().run_in_executor(
                None, sts.get_caller_identity
            )
            return ConnectorHealthResult(
                status="healthy",
                message="AWS credentials are valid.",
                safe_details={"account": caller.get("Account"), "arn_prefix": (caller.get("Arn") or "")[:20]},
            )
        except ImportError:
            pass
        except Exception as exc:
            err_str = str(exc)
            if "InvalidClientTokenId" in err_str or "SignatureDoesNotMatch" in err_str:
                return ConnectorHealthResult(
                    status="unhealthy",
                    message="AWS credentials are invalid.",
                    error_code="auth_failed",
                )
            logger.debug("S3 boto3 check failed: %s", type(exc).__name__)

        # Fallback: validate key ID format (AKIA... = 20 uppercase alphanumeric chars).
        if re.fullmatch(r"[A-Z0-9]{20}", access_key_id):
            return ConnectorHealthResult(
                status="configured",
                message="AWS credentials are present and correctly formatted. Install boto3 for a live reachability check.",
                safe_details={"live_check": False},
            )
        return ConnectorHealthResult(
            status="unhealthy",
            message="AWS_ACCESS_KEY_ID does not match the expected format (20 uppercase alphanumeric characters).",
            error_code="invalid_key_format",
        )


class _CloudinaryProvider:
    """Health check for Cloudinary credentials.

    Expects the vault secret to be the ``CLOUDINARY_URL`` in the form:
        ``cloudinary://api_key:api_secret@cloud_name``
    """

    provider_id = "cloudinary"
    supported_integration_ids = ["cloudinary"]

    _URL_PATTERN = re.compile(
        r"^cloudinary://(?P<api_key>[^:]+):(?P<api_secret>[^@]+)@(?P<cloud_name>[^/]+)$"
    )

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Cloudinary")

        m = self._URL_PATTERN.match((handle.value or "").strip())
        if not m:
            return ConnectorHealthResult(
                status="unhealthy",
                message="CLOUDINARY_URL is not in the expected format (cloudinary://api_key:api_secret@cloud_name).",
                error_code="invalid_url_format",
            )

        api_key = m.group("api_key")
        api_secret = m.group("api_secret")
        cloud_name = m.group("cloud_name")

        try:
            code, _ = await _http_check(
                "GET",
                f"https://api.cloudinary.com/v1_1/{cloud_name}/ping",
                basic_auth=(api_key, api_secret),
            )
        except httpx.ConnectError:
            return _connection_error("Cloudinary")
        except httpx.TimeoutException:
            return _timeout_error("Cloudinary")

        if code == 200:
            return ConnectorHealthResult(status="healthy", message="Cloudinary credentials are valid.")
        if code in (401, 403):
            return ConnectorHealthResult(
                status="unhealthy",
                message="Cloudinary credentials are invalid.",
                error_code="auth_failed",
            )
        return ConnectorHealthResult(
            status="unknown",
            message=f"Cloudinary API returned unexpected HTTP {code}.",
            error_code="unexpected_status",
        )


# --------------------------------------------------------------------------- #
# Database providers                                                           #
# --------------------------------------------------------------------------- #


class _MongoDBProvider:
    """Health check for a MongoDB connection string stored in the vault."""

    provider_id = "mongodb"
    supported_integration_ids = ["mongodb"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("MongoDB")

        uri = handle.value.strip()
        try:
            from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore[import]

            client: Any = AsyncIOMotorClient(
                uri,
                serverSelectionTimeoutMS=int(_TIMEOUT * 1000),
                connectTimeoutMS=int(_TIMEOUT * 1000),
            )
            await client.admin.command("ping")
            client.close()
            return ConnectorHealthResult(status="healthy", message="MongoDB connection is reachable.")
        except ImportError:
            return ConnectorHealthResult(
                status="configured",
                message="MongoDB URI is configured. Install motor to enable live reachability checks.",
                safe_details={"live_check": False},
            )
        except Exception as exc:
            err_str = str(exc)
            if "Authentication failed" in err_str or "auth" in err_str.lower():
                return ConnectorHealthResult(
                    status="unhealthy",
                    message="MongoDB authentication failed. Check the URI credentials.",
                    error_code="auth_failed",
                )
            return ConnectorHealthResult(
                status="unhealthy",
                message="MongoDB connection failed. Check the URI and network access.",
                error_code="connection_error",
            )


class _PostgreSQLProvider:
    """Health check for a PostgreSQL connection URL stored in the vault.

    Requires ``asyncpg`` which is an optional dependency. Returns
    ``"configured"`` when asyncpg is not installed.
    """

    provider_id = "postgres"
    supported_integration_ids = ["postgres", "postgresql"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("PostgreSQL")

        dsn = handle.value.strip()
        try:
            import asyncpg  # type: ignore[import]

            conn = await asyncpg.connect(dsn=dsn, timeout=_TIMEOUT)
            await conn.fetchval("SELECT 1")
            await conn.close()
            return ConnectorHealthResult(status="healthy", message="PostgreSQL connection is reachable.")
        except ImportError:
            return ConnectorHealthResult(
                status="configured",
                message="PostgreSQL URL is configured. Install asyncpg to enable live reachability checks.",
                safe_details={"live_check": False},
            )
        except Exception as exc:
            err_str = str(exc)
            if "password" in err_str.lower() or "auth" in err_str.lower() or "SASL" in err_str:
                return ConnectorHealthResult(
                    status="unhealthy",
                    message="PostgreSQL authentication failed. Check the connection URL credentials.",
                    error_code="auth_failed",
                )
            return ConnectorHealthResult(
                status="unhealthy",
                message="PostgreSQL connection failed. Check the URL and network access.",
                error_code="connection_error",
            )


# --------------------------------------------------------------------------- #
# Cache providers                                                              #
# --------------------------------------------------------------------------- #


class _RedisProvider:
    """Health check for a Redis connection URL stored in the vault."""

    provider_id = "redis"
    supported_integration_ids = ["redis"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Redis")

        url = handle.value.strip()
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            client = aioredis.from_url(url, socket_timeout=_TIMEOUT, socket_connect_timeout=_TIMEOUT)
            await client.ping()
            await client.aclose()
            return ConnectorHealthResult(status="healthy", message="Redis connection is reachable.")
        except ImportError:
            return ConnectorHealthResult(
                status="configured",
                message="Redis URL is configured. Ensure redis[asyncio] is installed for live checks.",
                safe_details={"live_check": False},
            )
        except Exception as exc:
            err_str = str(exc)
            if "WRONGPASS" in err_str or "NOAUTH" in err_str or "AUTH" in err_str:
                return ConnectorHealthResult(
                    status="unhealthy",
                    message="Redis authentication failed. Check the connection URL password.",
                    error_code="auth_failed",
                )
            return ConnectorHealthResult(
                status="unhealthy",
                message="Redis connection failed. Check the URL and network access.",
                error_code="connection_error",
            )


# --------------------------------------------------------------------------- #
# Auth providers                                                               #
# --------------------------------------------------------------------------- #


_GOOGLE_CLIENT_ID_RE = re.compile(r"^\d+-[a-zA-Z0-9_]+\.apps\.googleusercontent\.com$")
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_MICROSOFT_TENANT_RE = re.compile(
    r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|common|organizations|consumers)$"
)


class _GoogleOAuthProvider:
    """Format-validation health check for Google OAuth credentials.

    Validates that ``client_id`` follows the ``*.apps.googleusercontent.com``
    pattern and that both ``client_id`` and ``client_secret`` are non-empty.

    A live endpoint check would require a user-consent redirect and is
    intentionally out of scope for a background health probe.
    """

    provider_id = "google_oauth"
    supported_integration_ids = ["google_oauth"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Google OAuth")

        creds = _parse_secret_json(handle.value)
        client_id = (
            creds.get("client_id")
            or creds.get("GOOGLE_CLIENT_ID")
            or public_config.get("client_id")
            or public_config.get("GOOGLE_CLIENT_ID")
            or ""
        ).strip()
        client_secret = (
            creds.get("client_secret")
            or creds.get("GOOGLE_CLIENT_SECRET")
            or ""
        ).strip()

        if not client_id or not client_secret:
            return ConnectorHealthResult(
                status="not_configured",
                message="Google OAuth requires both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
                error_code="missing_credentials",
            )
        if not _GOOGLE_CLIENT_ID_RE.match(client_id):
            return ConnectorHealthResult(
                status="unhealthy",
                message="GOOGLE_CLIENT_ID does not match the expected format (xxx.apps.googleusercontent.com).",
                error_code="invalid_client_id_format",
            )
        return ConnectorHealthResult(
            status="configured",
            message="Google OAuth credentials are present and correctly formatted.",
            safe_details={"live_check": False},
        )


class _MicrosoftOAuthProvider:
    """Format-validation health check for Microsoft Entra / OAuth credentials.

    Validates that ``client_id`` is a valid GUID and that ``tenant_id`` (if
    provided) is a valid GUID or one of the well-known multi-tenant values
    (``common``, ``organizations``, ``consumers``).
    """

    provider_id = "microsoft_oauth"
    supported_integration_ids = ["microsoft_oauth"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Microsoft OAuth")

        creds = _parse_secret_json(handle.value)
        client_id = (
            creds.get("client_id")
            or creds.get("MICROSOFT_CLIENT_ID")
            or public_config.get("client_id")
            or public_config.get("MICROSOFT_CLIENT_ID")
            or ""
        ).strip()
        client_secret = (
            creds.get("client_secret")
            or creds.get("MICROSOFT_CLIENT_SECRET")
            or ""
        ).strip()
        tenant_id = (
            creds.get("tenant_id")
            or creds.get("MICROSOFT_TENANT_ID")
            or public_config.get("tenant_id")
            or public_config.get("MICROSOFT_TENANT_ID")
            or "common"
        ).strip()

        if not client_id or not client_secret:
            return ConnectorHealthResult(
                status="not_configured",
                message="Microsoft OAuth requires both MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET.",
                error_code="missing_credentials",
            )
        if not _GUID_RE.match(client_id):
            return ConnectorHealthResult(
                status="unhealthy",
                message="MICROSOFT_CLIENT_ID must be a valid GUID.",
                error_code="invalid_client_id_format",
            )
        if not _MICROSOFT_TENANT_RE.match(tenant_id):
            return ConnectorHealthResult(
                status="unhealthy",
                message=(
                    "MICROSOFT_TENANT_ID must be a valid GUID or one of: common, organizations, consumers."
                ),
                error_code="invalid_tenant_id_format",
            )
        return ConnectorHealthResult(
            status="configured",
            message="Microsoft OAuth credentials are present and correctly formatted.",
            safe_details={"live_check": False},
        )


# --------------------------------------------------------------------------- #
# Analytics providers                                                          #
# --------------------------------------------------------------------------- #


class _SegmentProvider:
    """Presence-only health check for Segment write keys.

    Segment does not expose a public endpoint to validate a write key without
    emitting a tracking event. The check confirms the key is present and
    non-empty. ``"configured"`` is the strongest claim we can make.
    """

    provider_id = "segment"
    supported_integration_ids = ["segment"]

    async def check(
        self,
        *,
        connector: dict[str, Any],
        secret_reader: ConnectorSecretReader,
        public_config: dict[str, Any],
        context: ConnectorHealthContext,
    ) -> ConnectorHealthResult:
        handle = await secret_reader.get_secret()
        if not handle.available or not handle.value:
            return _no_secret("Segment")
        write_key = (handle.value or "").strip()
        if not write_key:
            return ConnectorHealthResult(
                status="not_configured",
                message="Segment write key is empty.",
                error_code="missing_credentials",
            )
        return ConnectorHealthResult(
            status="configured",
            message="Segment write key is configured. No public validation endpoint is available.",
            safe_details={"live_check": False},
        )


# --------------------------------------------------------------------------- #
# Registration                                                                 #
# --------------------------------------------------------------------------- #

_BUILTIN_PROVIDERS: list[ConnectorHealthProvider] = [
    _OpenAIProvider(),       # type: ignore[list-item]
    _AnthropicProvider(),    # type: ignore[list-item]
    _GeminiProvider(),       # type: ignore[list-item]
    _MozaiksPayProvider(),   # type: ignore[list-item]
    _ResendProvider(),       # type: ignore[list-item]
    _SendGridProvider(),     # type: ignore[list-item]
    _TwilioProvider(),       # type: ignore[list-item]
    _SlackProvider(),        # type: ignore[list-item]
    _GitHubProvider(),       # type: ignore[list-item]
    _S3Provider(),           # type: ignore[list-item]
    _CloudinaryProvider(),   # type: ignore[list-item]
    _MongoDBProvider(),      # type: ignore[list-item]
    _PostgreSQLProvider(),   # type: ignore[list-item]
    _RedisProvider(),        # type: ignore[list-item]
    _GoogleOAuthProvider(),  # type: ignore[list-item]
    _MicrosoftOAuthProvider(),  # type: ignore[list-item]
    _SegmentProvider(),      # type: ignore[list-item]
]


def register_builtin_providers() -> None:
    """Register all built-in catalog connector health providers.

    Call this once at process startup (e.g. inside the FastAPI lifespan).
    Safe to call multiple times — subsequent calls re-register idempotently.
    """
    for provider in _BUILTIN_PROVIDERS:
        register_connector_health_provider(provider)
    logger.debug(
        "Registered %d built-in connector health providers: %s",
        len(_BUILTIN_PROVIDERS),
        ", ".join(p.provider_id for p in _BUILTIN_PROVIDERS),
    )


__all__ = [
    "register_builtin_providers",
]
