"""SMTP delivery mechanics; the reacting module owns recipients and opt-in policy.

Any SMTP service works: self-hosted, third party, or supplied by a host such as
Mozaiks Cloud. Only deployment configuration selects the service.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import smtplib
import ssl
from datetime import UTC, datetime
from email.errors import MessageError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid

from mozaiksai.core.secrets import resolve_secret

TIMEOUT_SECONDS = 15


def _setting(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _set_mailbox(message: EmailMessage, header: str, value: str) -> Address:
    """Set an address header that names exactly one complete mailbox."""
    try:
        message[header] = value
        parsed = message[header]
        addresses = parsed.addresses
    except (MessageError, IndexError) as exc:
        raise ValueError(f"{header} must be one complete mailbox address") from exc
    if parsed.defects or len(addresses) != 1 or not addresses[0].username or not addresses[0].domain:
        raise ValueError(f"{header} must be one complete mailbox address")
    address: Address = addresses[0]
    return address


def _port(security: str) -> int:
    raw = _setting("SMTP_PORT", "465" if security == "ssl" else "587")
    if not raw.isdecimal() or not 1 <= int(raw) <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535")
    return int(raw)


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _deliver(to: str, subject: str, body: str) -> bool:
    host = _setting("SMTP_HOST")
    sender = _setting("SMTP_FROM")
    if not host or not sender:
        raise ValueError("Configure SMTP_HOST and SMTP_FROM before sending email")
    security = _setting("SMTP_SECURITY", "starttls").lower()
    if security not in {"starttls", "ssl", "none"}:
        raise ValueError("SMTP_SECURITY must be starttls, ssl, or none")
    port = _port(security)
    username = _setting("SMTP_USER")
    if _setting("SMTP_PASSWORD") and not username:
        raise ValueError("Configure SMTP_USER and SMTP_PASSWORD together")
    if security == "none" and (username or not _is_loopback(host)):
        raise ValueError("Plaintext SMTP is only allowed for a credential-free loopback sink")

    message = EmailMessage()
    from_address = _set_mailbox(message, "From", sender)
    to_address = _set_mailbox(message, "To", to)
    message["Subject"] = subject
    message["Date"] = datetime.now(UTC)
    message["Message-ID"] = make_msgid(domain=from_address.domain)
    message.set_content(body)

    password = resolve_secret("SMTP_PASSWORD") if username else ""
    client: smtplib.SMTP
    if security == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS)
    with client as smtp:
        if security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if username:
            smtp.login(username, password)
        return not smtp.send_message(message, from_addr=from_address.addr_spec, to_addrs=[to_address.addr_spec])


async def send_email(*, to: str, subject: str, body: str) -> bool:
    """Return True only after the configured SMTP server accepts the message.

    This is server acceptance, not proof of inbox delivery. SMTP_HOST and
    SMTP_FROM are required. SMTP_PASSWORD resolves through the app secret
    contract, so it may come from the environment or a declared vault entry.
    Plaintext is restricted to a credential-free loopback development sink.
    """
    return await asyncio.to_thread(_deliver, to, subject, body)
