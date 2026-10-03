"""One fast MongoDB reachability check for CLI commands that start a host.

In local development the runtime treats MongoDB as best-effort at startup: with
an unreachable ``MONGO_URI`` it waits out the driver's 30 second server
selection timeout several times before it listens, and then answers health
probes slowly with 503. Commands that start a host check once, quickly, before
anything starts, and stop with a message that names ``MONGO_URI``.

``mozaiks serve`` is also a container entrypoint, so what this module returns
for printing lands in logs. A connection string is shown only through
:func:`redact_mongo_uri`, and the driver's own message is repeated only when it
describes a connection attempt to hosts that function would show.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from typing import Any

PREFLIGHT_TIMEOUT_ENV = "MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS"
_DEFAULT_TIMEOUT_MS = 5000

_SCHEMES = ("mongodb+srv://", "mongodb://")
_NOT_SHOWN = "<not shown: not a well-formed MongoDB URI>"
# A host name, an IPv4 address, a bracketed IPv6 address, or a percent-encoded
# socket path, each with an optional port.
_HOST = re.compile(r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._%-]+)(?::[0-9]{1,5})?")
_DATABASE = re.compile(r"[A-Za-z0-9_-]+")
_URI_IN_TEXT = re.compile(r"mongodb(?:\+srv)?://\S+")
_ESCAPE_HINT = (
    "Check the URI's format: special characters in the username or password "
    "(such as @ : / ? #) must be percent-encoded."
)


class MongoUriRejectedError(Exception):
    """The driver refused the URI itself, before attempting any connection."""

    def __init__(self, driver_error: str) -> None:
        super().__init__(driver_error)
        self.driver_error = driver_error


def preflight_timeout_ms(env: Mapping[str, str]) -> int:
    raw_value = str(env.get(PREFLIGHT_TIMEOUT_ENV) or "").strip()
    if not raw_value:
        return _DEFAULT_TIMEOUT_MS
    try:
        return max(1000, int(raw_value))
    except ValueError:
        return _DEFAULT_TIMEOUT_MS


def _printable_parts(uri: str) -> tuple[str, bool, str, str] | None:
    """Split a URI into ``(scheme, has_credentials, hosts, database)``.

    Returns ``None`` unless the hosts can be identified with certainty. An
    unescaped ``@``, ``/`` or ``?`` in a password moves credential text out of
    the credential section, so any ``@`` beyond the single one that ends that
    section makes the whole URI unprintable rather than partly shown.
    """
    scheme = next((prefix for prefix in _SCHEMES if uri.startswith(prefix)), None)
    if scheme is None:
        return None
    rest = uri[len(scheme):]
    cut = min((index for index in (rest.find("/"), rest.find("?")) if index != -1), default=len(rest))
    authority, tail = rest[:cut], rest[cut:]
    if "@" in tail or authority.count("@") > 1:
        return None
    _credentials, separator, hosts = authority.rpartition("@")
    if not all(_HOST.fullmatch(host) for host in hosts.split(",")):
        return None
    database = tail[1:].split("?", 1)[0] if tail.startswith("/") else ""
    return scheme, bool(separator), hosts, database if _DATABASE.fullmatch(database) else ""


def redact_mongo_uri(uri: str) -> str:
    """Return the only form of a MongoDB URI that may be printed.

    A well-formed URI prints as its scheme, ``***@`` in place of credentials,
    its hosts and its database name; the query string is never printed. A URI
    whose hosts cannot be identified with certainty prints as its scheme and a
    placeholder, and text that is not a MongoDB URI as the placeholder alone.
    """
    parts = _printable_parts(uri)
    if parts is None:
        scheme = next((prefix for prefix in _SCHEMES if uri.startswith(prefix)), "")
        return f"{scheme}{_NOT_SHOWN}"
    scheme, has_credentials, hosts, database = parts
    return f"{scheme}{'***@' if has_credentials else ''}{hosts}{'/' + database if database else ''}"


def short_error_message(exc: Exception) -> str:
    message = _URI_IN_TEXT.sub(
        lambda match: redact_mongo_uri(match.group(0)), str(exc).replace("\n", " ")
    )
    message = re.sub(r"\s*\(configured timeouts:[^)]+\)", "", message)
    for marker in (", Timeout:", " Timeout:", ", Topology Description:", " Topology Description:"):
        index = message.find(marker)
        if index != -1:
            message = message[:index]
            break
    return f"{type(exc).__name__}: {message.strip()}"


def ping_mongo_uri(uri: str, *, timeout_ms: int) -> None:
    """Ping the server at ``uri``.

    Raises :class:`MongoUriRejectedError` when the driver refuses the URI
    before connecting, and the driver's own error when the ping fails.
    """
    from pymongo import MongoClient

    try:
        # The driver reports an unusable URI option as a warning that quotes
        # the option's value; nothing from the query string may be printed.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            client: MongoClient[dict[str, Any]] = MongoClient(
                uri,
                serverSelectionTimeoutMS=timeout_ms,
                connectTimeoutMS=timeout_ms,
            )
    except Exception as exc:
        raise MongoUriRejectedError(type(exc).__name__) from None
    try:
        client.admin.command("ping")
    finally:
        client.close()


def mongo_unreachable_reason(uri: str, *, timeout_ms: int) -> str | None:
    """Ping ``uri`` once; return ``None`` when it answers, else a short reason.

    The driver reads a malformed URI leniently and can take part of a password
    for a host, a port or a database name, then quote it in its error. Its
    message is therefore repeated only for a failed connection attempt to a
    URI whose hosts :func:`redact_mongo_uri` shows.
    """
    try:
        ping_mongo_uri(uri, timeout_ms=timeout_ms)
    except MongoUriRejectedError as exc:
        return (
            f"{exc.driver_error}: the MongoDB driver rejected MONGO_URI before connecting "
            "(for mongodb+srv:// this includes the DNS lookup of the host). Its message is "
            f"not shown because it can repeat parts of the URI. {_ESCAPE_HINT}"
        )
    except Exception as exc:
        if _printable_parts(uri) is None:
            return (
                f"{type(exc).__name__}: the driver's message is not shown because MONGO_URI is "
                f"not a well-formed MongoDB URI and the message can repeat parts of it. {_ESCAPE_HINT}"
            )
        return short_error_message(exc)
    return None
