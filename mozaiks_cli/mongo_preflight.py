"""One fast MongoDB reachability check for CLI commands that start a host.

In local development the runtime treats MongoDB as best-effort at startup: with
an unreachable ``MONGO_URI`` it waits out the driver's 30 second server
selection timeout several times before it listens, and then answers health
probes slowly with 503. Commands that start a host check once, quickly, before
anything starts, and stop with a message that names ``MONGO_URI``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

PREFLIGHT_TIMEOUT_ENV = "MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS"
_DEFAULT_TIMEOUT_MS = 5000


def preflight_timeout_ms(env: Mapping[str, str]) -> int:
    raw_value = str(env.get(PREFLIGHT_TIMEOUT_ENV) or "").strip()
    if not raw_value:
        return _DEFAULT_TIMEOUT_MS
    try:
        return max(1000, int(raw_value))
    except ValueError:
        return _DEFAULT_TIMEOUT_MS


def redact_mongo_uri(uri: str) -> str:
    """Hide the credentials of a MongoDB URI so it can be printed."""
    return re.sub(r"(mongodb(?:\+srv)?://)([^/@\s]+@)", r"\1***@", uri)


def short_error_message(exc: Exception) -> str:
    message = redact_mongo_uri(str(exc).replace("\n", " "))
    message = re.sub(r"\s*\(configured timeouts:[^)]+\)", "", message)
    for marker in (", Timeout:", " Timeout:", ", Topology Description:", " Topology Description:"):
        index = message.find(marker)
        if index != -1:
            message = message[:index]
            break
    return f"{type(exc).__name__}: {message.strip()}"


def ping_mongo_uri(uri: str, *, timeout_ms: int) -> None:
    from pymongo import MongoClient

    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
    )
    try:
        client.admin.command("ping")
    finally:
        client.close()


def mongo_unreachable_reason(uri: str, *, timeout_ms: int) -> str | None:
    """Ping ``uri`` once; return ``None`` when it answers, else a short reason."""
    try:
        ping_mongo_uri(uri, timeout_ms=timeout_ms)
    except Exception as exc:
        return short_error_message(exc)
    return None
