"""One fast MongoDB reachability check for CLI commands that start a host.

In local development the runtime treats MongoDB as best-effort at startup: with
an unreachable ``MONGO_URI`` it waits out the driver's 30 second server
selection timeout several times before it listens, and then answers health
probes slowly with 503. Commands that start a host check once, quickly, before
anything starts, and stop with a message that names ``MONGO_URI``.

``mozaiks serve`` is also a container entrypoint, so what this module returns
for printing lands in logs. :func:`mongo_unreachable` returns the only two
texts a command may print: the connection string as :func:`redact_mongo_uri`
and the driver's verdict allow it to be shown, and a reason that repeats the
driver's own message only when it describes a connection attempt to hosts
that are shown.

What this can and cannot promise. While the URI still has the ``@`` that ends
its credential section, no text of that section is printed, whatever
characters the password contains. When that ``@`` is gone (the value was cut
off before it, or it was percent-encoded) what remains is a different URI in
which credential text stands where the hosts and the database name are read.
A percent-encoded host name and, next to credentials, a host with no dot and
no port are not shown for that reason. Text that reads as an address with a
dot, a port or IPv6 brackets cannot be told apart from a real address and is
shown, as is any host name in a value that has no ``@`` left.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

PREFLIGHT_TIMEOUT_ENV = "MOZAIKS_MONGO_PREFLIGHT_TIMEOUT_MS"
_DEFAULT_TIMEOUT_MS = 5000

_SCHEMES = ("mongodb+srv://", "mongodb://")
_NOT_SHOWN = "<not shown: not a well-formed MongoDB URI>"
_HOST_NOT_SHOWN = "<host not shown>"
# What the driver dials: a host name or an IPv4 address (labels joined by
# single dots), or a bracketed IPv6 address, each with an optional port, or a
# Unix socket path. Percent-encoding is read only in the slashes of a socket
# path: "%40" in a host name is the "@" that ends the credentials, encoded in
# place of the one in the password. An IPv6 zone index, which the driver also
# reads, is not shown.
_LABEL = r"[A-Za-z0-9_](?:[A-Za-z0-9_-]*[A-Za-z0-9_])?"
_HOST = re.compile(
    rf"(?:\[[0-9A-Fa-f:.]+\]|{_LABEL}(?:\.{_LABEL})*)(?::[0-9]{{1,5}})?"
    r"|(?:%2[Ff][A-Za-z0-9._-]+)+\.sock"
)
_DATABASE = re.compile(r"[A-Za-z0-9_-]+")
_URI_IN_TEXT = re.compile(r"mongodb(?:\+srv)?://\S+")
_ESCAPE_HINT = (
    "Check the URI's format: special characters in the username or password "
    "(such as @ : / ? #) must be percent-encoded, and options follow a / after the "
    "hosts (mongodb://host:27017/?name=value)."
)
_SRV_HINT = (
    "For mongodb+srv:// the driver looks up the host shown in DNS before it connects, and "
    "this is most often that lookup failing: check the host name and that this machine can "
    "resolve it."
)
_OPTIONS_HINT = (
    "The driver read the URI and refused what its options ask for, such as options that "
    "conflict with each other or with the number of hosts."
)
_HOST_NOT_SHOWN_HINT = (
    "MONGO_URI has credentials and a host with no dot and no port, which is also how the rest "
    "of a password reads after an unescaped @. If that is the real host, write its port (for "
    "example mongo:27017) to have it shown. Otherwise percent-encode the special characters "
    "in the username and password."
)


class MongoUriRejectedError(Exception):
    """The driver refused the URI itself, before attempting any connection.

    ``malformed`` is false only for a refusal the driver does not report as
    an invalid URI: a failed SRV lookup, options that conflict.
    """

    def __init__(self, driver_error: str, *, malformed: bool) -> None:
        super().__init__(driver_error)
        self.driver_error = driver_error
        self.malformed = malformed


@dataclass(frozen=True)
class MongoUnreachable:
    """Why the server did not answer, and how its URI may be printed."""

    shown_uri: str
    reason: str


class _Parts(NamedTuple):
    scheme: str
    has_credentials: bool
    hosts: str
    database: str

    @property
    def host_may_be_password_text(self) -> bool:
        """Credentials, then a host with no dot, no port and no brackets."""
        return self.has_credentials and any(
            "." not in host and ":" not in host for host in self.hosts.split(",")
        )


def preflight_timeout_ms(env: Mapping[str, str]) -> int:
    raw_value = str(env.get(PREFLIGHT_TIMEOUT_ENV) or "").strip()
    if not raw_value:
        return _DEFAULT_TIMEOUT_MS
    try:
        return max(1000, int(raw_value))
    except ValueError:
        return _DEFAULT_TIMEOUT_MS


def _scheme_of(uri: str) -> str:
    return next((prefix for prefix in _SCHEMES if uri.startswith(prefix)), "")


def _printable_parts(uri: str) -> _Parts | None:
    """Split a URI into its scheme, hosts and database name.

    Returns ``None`` on any sign that the URI does not read the way it was
    meant. An unescaped ``@``, ``/`` or ``?`` in a password moves credential
    text out of the credential section, so any ``@`` beyond the single one
    that ends that section, a ``?`` before the first ``/``, a host that is not
    an address, and a database segment that is not a plain name each make the
    whole URI unprintable rather than partly shown.
    """
    scheme = _scheme_of(uri)
    if not scheme:
        return None
    rest = uri[len(scheme):]
    slash, question = rest.find("/"), rest.find("?")
    # Options follow a "/" after the hosts. Without it, where the hosts end is
    # each driver version's own reading: older ones read up to a later "/" and
    # name the option text before it as a host in their connection errors.
    if question != -1 and (slash == -1 or question < slash):
        return None
    authority, tail = (rest[:slash], rest[slash:]) if slash != -1 else (rest, "")
    if "@" in tail or authority.count("@") > 1:
        return None
    _credentials, separator, hosts = authority.rpartition("@")
    if not all(_HOST.fullmatch(host) for host in hosts.split(",")):
        return None
    database = tail[1:].split("?", 1)[0] if tail.startswith("/") else ""
    if database and not _DATABASE.fullmatch(database):
        return None
    return _Parts(scheme, bool(separator), hosts, database)


def redact_mongo_uri(uri: str) -> str:
    """Return the most of a MongoDB URI that may be printed.

    A well-formed URI prints as its scheme, ``***@`` in place of credentials,
    its hosts and its database name; the query string is never printed. Next
    to credentials, a host with no dot and no port prints as a placeholder,
    and the database name with it. A URI with any sign that it does not read
    the way it was meant prints as its scheme and a placeholder, and text that
    is not a MongoDB URI as the placeholder alone.
    """
    parts = _printable_parts(uri)
    if parts is None:
        return f"{_scheme_of(uri)}{_NOT_SHOWN}"
    if parts.host_may_be_password_text:
        return f"{parts.scheme}***@{_HOST_NOT_SHOWN}"
    credentials = "***@" if parts.has_credentials else ""
    database = f"/{parts.database}" if parts.database else ""
    return f"{parts.scheme}{credentials}{parts.hosts}{database}"


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
    from pymongo.errors import ConfigurationError, InvalidURI

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
        # InvalidURI is itself a ConfigurationError. The others (a failed SRV
        # lookup, options that conflict) are not about the form of the URI.
        well_formed = isinstance(exc, ConfigurationError) and not isinstance(exc, InvalidURI)
        raise MongoUriRejectedError(type(exc).__name__, malformed=not well_formed) from None
    try:
        client.admin.command("ping")
    finally:
        client.close()


def mongo_unreachable(uri: str, *, timeout_ms: int) -> MongoUnreachable | None:
    """Ping ``uri`` once; return ``None`` when it answers.

    Otherwise return the reason and the form of the URI to print with it. The
    driver reads a malformed URI leniently and can take part of a password for
    a host, a port or a database name, then quote it in its error. So hosts
    are shown only for a URI that :func:`redact_mongo_uri` shows them for and
    that the driver does not reject as malformed, and the driver's message is
    repeated only for a failed connection attempt to hosts that are shown.
    """
    parts = _printable_parts(uri)
    shown_uri = redact_mongo_uri(uri)
    hint = _HOST_NOT_SHOWN_HINT if parts is not None and parts.host_may_be_password_text else _ESCAPE_HINT
    try:
        ping_mongo_uri(uri, timeout_ms=timeout_ms)
    except MongoUriRejectedError as exc:
        if exc.malformed:
            shown_uri, hint = f"{_scheme_of(uri)}{_NOT_SHOWN}", _ESCAPE_HINT
        elif parts is not None and not parts.host_may_be_password_text:
            # The driver read the URI, whose hosts are shown, and refused what it asks for.
            hint = _SRV_HINT if parts.scheme == "mongodb+srv://" else _OPTIONS_HINT
        reason = (
            f"{exc.driver_error}: the MongoDB driver rejected MONGO_URI before connecting. Its "
            f"message is not shown because it can repeat parts of the URI. {hint}"
        )
    except Exception as exc:
        if parts is None:
            reason = (
                f"{type(exc).__name__}: the driver's message is not shown because MONGO_URI is "
                f"not a well-formed MongoDB URI and the message can repeat parts of it. {hint}"
            )
        elif parts.host_may_be_password_text:
            reason = f"{type(exc).__name__}: the driver's message is not shown because it names the host. {hint}"
        else:
            reason = short_error_message(exc)
    else:
        return None
    return MongoUnreachable(shown_uri=shown_uri, reason=reason)
